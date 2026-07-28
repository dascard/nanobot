"""Sandbox 管理状态、无损关闭开关与单次运行取消。"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import (
    AdminAuditLog,
    Asset,
    ChatLog,
    ConversationTurn,
    SandboxAccessGrant,
    SandboxAdminOperation,
    SandboxLease,
    SandboxRun,
    SystemSetting,
    Workspace,
    WorkspaceAsset,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
    get_db,
)
from core.sandbox.client import (
    HttpSandboxdAdminBackend,
    HttpSandboxdBackend,
)
from core.sandbox.access_policy import canonical_sandbox_identity
from core.sandbox.admin_service import (
    SandboxAdminRequestError,
    SandboxAdminService,
)
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.execution_profiles import load_execution_profile_registry
from core.sandbox.tool_service import resolve_sandbox_setting
from core.settings_service import settings
from core.time_utils import db_now_naive


router = APIRouter(prefix="/sandbox")
_RUN_STATUS = Literal["pending", "running", "completed", "failed", "cancelled"]


class SandboxKillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    reason: str = Field(default="", max_length=255)


class SandboxFeatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    exec_enabled: bool
    group_enabled: bool | None = None
    reason: str = Field(default="", max_length=255)


class SandboxAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[!-~]+$")
    platform: str = Field(default="qq", min_length=1, max_length=32)
    chat_type: Literal["private", "group"] = "private"
    session_id: str = Field(min_length=1, max_length=255)
    capability: Literal["off", "workspace", "assets", "exec"]
    execution_profile: Literal["restricted", "developer"] = "restricted"
    quota_bytes: int | None = Field(
        default=None,
        ge=1024 * 1024,
        le=1024 * 1024 * 1024 * 1024,
    )
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(default="", max_length=255)


class SandboxQuotaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[!-~]+$")
    quota_bytes: int = Field(
        ge=1024 * 1024,
        le=1024 * 1024 * 1024 * 1024,
    )
    reason: str = Field(default="", max_length=255)


class SandboxLeaseActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    reason: str = Field(default="", max_length=255)


def _sandbox_backend(db: Session) -> HttpSandboxdBackend:
    return HttpSandboxdBackend(
        socket_path=str(resolve_sandbox_setting(db, "sandbox.sandboxd_socket")),
        token_file=str(resolve_sandbox_setting(db, "sandbox.sandboxd_token_file")),
        timeout_seconds=float(resolve_sandbox_setting(
            db,
            "sandbox.backend_timeout_seconds",
        )),
        run_timeout_seconds=float(resolve_sandbox_setting(
            db,
            "sandbox.run_timeout_seconds",
        )),
    )


def _sandbox_admin_backend(db: Session) -> HttpSandboxdAdminBackend:
    return HttpSandboxdAdminBackend(
        socket_path=str(resolve_sandbox_setting(db, "sandbox.sandboxd_socket")),
        token_file=str(resolve_sandbox_setting(
            db,
            "sandbox.sandboxd_admin_token_file",
        )),
        timeout_seconds=float(resolve_sandbox_setting(
            db,
            "sandbox.backend_timeout_seconds",
        )),
        run_timeout_seconds=float(resolve_sandbox_setting(
            db,
            "sandbox.run_timeout_seconds",
        )),
    )


def _safe_probe_error(error: SandboxServiceError) -> dict[str, object]:
    return {
        "ok": False,
        "summary": error.summary,
        "error": {
            "code": error.code.value,
            "retryable": error.retryable,
        },
    }


def _controller_status(db: Session) -> dict[str, object]:
    backend = _sandbox_backend(db)
    try:
        try:
            health = backend.health()
            health_state: dict[str, object] = {
                "ok": health.get("status") == "success",
                "service": str(
                    (health.get("data") or {}).get("service")
                    if isinstance(health.get("data"), dict)
                    else ""
                )[:64],
            }
        except SandboxServiceError as exc:
            health_state = _safe_probe_error(exc)

        try:
            ready = backend.ready()
            raw_data = ready.get("data") if isinstance(ready.get("data"), dict) else {}
            raw_profiles = (
                raw_data.get("profiles")
                if isinstance(raw_data.get("profiles"), dict)
                else {}
            )
            profiles: dict[str, dict[str, object]] = {}
            for profile_id in (
                "restricted",
                "developer",
                "trusted_developer",
            ):
                item = raw_profiles.get(profile_id)
                if not isinstance(item, dict):
                    continue
                profiles[profile_id] = {
                    "ready": item.get("ready") is True,
                    "grantable": item.get("grantable") is True,
                    "execution_mode": str(
                        item.get("execution_mode") or ""
                    )[:16],
                    "image_id": str(item.get("image_id") or "")[:72],
                    "apparmor_profile": str(
                        item.get("apparmor_profile") or ""
                    )[:128],
                    "error_code": str(item.get("error_code") or "")[:64],
                }
            ready_state: dict[str, object] = {
                "ok": ready.get("status") == "success",
                "docker": raw_data.get("docker") is True,
                "catalog_generation": str(
                    raw_data.get("catalog_generation") or ""
                )[:64],
                "policy_sha256": str(
                    raw_data.get("policy_sha256") or ""
                )[:64],
                "profiles": profiles,
                "project_quota_ready": (
                    raw_data.get("project_quota_ready") is True
                ),
                "disk_used_percent": raw_data.get("disk_used_percent"),
                "disk_free_bytes": raw_data.get("disk_free_bytes"),
            }
            try:
                ready_state["policy_matches_server"] = (
                    load_execution_profile_registry()
                    .policy_matches_controller(raw_data)
                )
            except SandboxServiceError:
                ready_state["policy_matches_server"] = False
        except SandboxServiceError as exc:
            ready_state = _safe_probe_error(exc)
        return {"health": health_state, "ready": ready_state}
    finally:
        backend.close()


def _run_summary(row: SandboxRun) -> dict[str, object]:
    """只返回账本元数据，不返回命令、stdout、stderr 或宿主路径。"""

    return {
        "run_id": row.run_id,
        "workspace_id": row.workspace_id,
        "profile_id": row.profile_id,
        "execution_mode": row.execution_mode,
        "lease_id": row.lease_id,
        "process_state": row.process_state,
        "trace_id": row.trace_id,
        "agent_run_id": row.agent_run_id,
        "tool_call_id": row.tool_call_id,
        "image_digest": row.image_digest,
        "status": row.status,
        "exit_code": row.exit_code,
        "termination_reason": row.termination_reason,
        "cpu_time_ms": int(row.cpu_time_ms or 0),
        "peak_memory_bytes": int(row.peak_memory_bytes or 0),
        "stdout_bytes": int(row.stdout_bytes or 0),
        "stderr_bytes": int(row.stderr_bytes or 0),
        "stdout_truncated": bool(row.stdout_truncated),
        "stderr_truncated": bool(row.stderr_truncated),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _recent_runs(
    db: Session,
    *,
    statuses: tuple[str, ...] | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    query = db.query(SandboxRun)
    if statuses:
        query = query.filter(SandboxRun.status.in_(statuses))
    rows = query.order_by(SandboxRun.created_at.desc()).limit(limit).all()
    return [_run_summary(row) for row in rows]


def _usage_summary(db: Session) -> dict[str, int]:
    workspace_count, used_bytes, quota_bytes = db.query(
        func.count(Workspace.id),
        func.coalesce(func.sum(Workspace.used_bytes), 0),
        func.coalesce(func.sum(Workspace.quota_bytes), 0),
    ).one()
    asset_count, asset_bytes = db.query(
        func.count(Asset.sha256),
        func.coalesce(func.sum(Asset.size_bytes), 0),
    ).one()
    asset_link_count = db.query(func.count(WorkspaceAsset.id)).scalar()
    return {
        "workspace_count": int(workspace_count or 0),
        "workspace_used_bytes": int(used_bytes or 0),
        "workspace_quota_bytes": int(quota_bytes or 0),
        "asset_count": int(asset_count or 0),
        "asset_physical_bytes": int(asset_bytes or 0),
        "asset_link_count": int(asset_link_count or 0),
    }


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _operation_summary(row: SandboxAdminOperation) -> dict[str, object]:
    return {
        "operation_id": row.operation_id,
        "request_id": row.request_id,
        "operation_type": row.operation_type,
        "chat_stream_id": row.chat_stream_id,
        "workspace_id": row.workspace_id,
        "desired_capability": row.desired_capability,
        "previous_capability": row.previous_capability,
        "desired_quota_bytes": int(row.desired_quota_bytes or 0),
        "expected_grant_version": row.expected_grant_version,
        "expected_quota_generation": row.expected_quota_generation,
        "status": row.status,
        "step": row.step,
        "attempt_count": int(row.attempt_count or 0),
        "max_attempts": int(row.max_attempts or 0),
        "error_code": row.error_code,
        "error_summary": row.error_summary,
        "reason": row.reason,
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "next_attempt_at": _iso(row.next_attempt_at),
        "updated_at": _iso(row.updated_at),
    }


def _grant_summary(
    grant: SandboxAccessGrant,
    workspace: Workspace | None,
    binding: WorkspaceQuotaBinding | None,
    runtime_binding: WorkspaceRuntimeQuotaBinding | None,
) -> dict[str, object]:
    return {
        "id": grant.id,
        "chat_stream_id": grant.chat_stream_id,
        "platform": grant.platform,
        "chat_type": grant.chat_type,
        "session_id": grant.external_session_id,
        "workspace_id": grant.workspace_id,
        "capability": grant.capability_level,
        "execution_profile": grant.execution_profile,
        "status": grant.status,
        "version": int(grant.version),
        "reason": grant.reason,
        "updated_by": grant.updated_by,
        "updated_at": _iso(grant.updated_at),
        "workspace": None if workspace is None else {
            "status": workspace.status,
            "used_bytes": int(workspace.used_bytes or 0),
            "quota_bytes": int(workspace.quota_bytes or 0),
            "last_accessed_at": _iso(workspace.last_accessed_at),
        },
        "quota": None if binding is None else {
            "project_id": int(binding.project_id),
            "desired_quota_bytes": int(binding.desired_quota_bytes),
            "applied_quota_bytes": int(binding.applied_quota_bytes or 0),
            "status": binding.status,
            "generation": int(binding.generation),
            "last_error_code": binding.last_error_code,
            "last_error_summary": binding.last_error_summary,
            "last_applied_at": _iso(binding.last_applied_at),
        },
        "runtime_quota": None if runtime_binding is None else {
            "project_id": int(runtime_binding.project_id),
            "desired_quota_bytes": int(
                runtime_binding.desired_quota_bytes
            ),
            "applied_quota_bytes": int(
                runtime_binding.applied_quota_bytes or 0
            ),
            "status": runtime_binding.status,
            "generation": int(runtime_binding.generation),
            "last_error_code": runtime_binding.last_error_code,
            "last_error_summary": runtime_binding.last_error_summary,
            "last_applied_at": _iso(runtime_binding.last_applied_at),
        },
    }


def _admin_error(error: SandboxAdminRequestError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.summary},
    )


def _prepare_sync_operation(
    db: Session,
    *,
    request_id: str,
    operation_type: str,
    chat_stream_id: str = "",
    workspace_id: str | None = None,
    reason: str = "",
    actor: str = "admin",
) -> tuple[SandboxAdminOperation, bool]:
    """为同步管理副作用建立幂等账本；异步 runner 不认领这些类型。"""

    existing = (
        db.query(SandboxAdminOperation)
        .filter(SandboxAdminOperation.request_id == request_id)
        .one_or_none()
    )
    if existing is not None:
        expected = (
            operation_type,
            str(chat_stream_id or ""),
            str(workspace_id or ""),
            str(reason or "")[:255],
        )
        actual = (
            str(existing.operation_type or ""),
            str(existing.chat_stream_id or ""),
            str(existing.workspace_id or ""),
            str(existing.reason or ""),
        )
        if actual != expected:
            raise HTTPException(
                409,
                {
                    "code": "idempotency_conflict",
                    "message": "同一 request_id 已用于不同 Sandbox 管理请求",
                },
            )
        if str(existing.status) == "succeeded":
            return existing, True
        raise HTTPException(
            409,
            {
                "code": "operation_not_replayable",
                "message": (
                    "同一 request_id 对应的 Sandbox 管理操作尚未成功完成"
                ),
                "status": str(existing.status),
            },
        )

    now = db_now_naive()
    operation = SandboxAdminOperation(
        operation_id=f"sbxop_{secrets.token_hex(16)}",
        request_id=request_id,
        operation_type=operation_type,
        chat_stream_id=str(chat_stream_id or ""),
        workspace_id=workspace_id,
        status="running",
        step="calling_sandboxd",
        attempt_count=1,
        max_attempts=1,
        locked_by="admin-api",
        reason=str(reason or "")[:255],
        created_by=str(actor or "admin")[:128],
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(operation)
    db.flush()
    return operation, False


def _fail_sync_operation(
    db: Session,
    operation_id: str,
    *,
    code: str,
    summary: str,
) -> None:
    db.rollback()
    operation = db.get(SandboxAdminOperation, operation_id)
    if operation is None or str(operation.status) == "succeeded":
        return
    now = db_now_naive()
    operation.status = "failed"
    operation.step = "failed"
    operation.error_code = str(code or "runtime_unavailable")[:64]
    operation.error_summary = str(summary or "Sandbox 管理操作失败")[:255]
    operation.locked_by = ""
    operation.lease_token = ""
    operation.lease_expires_at = None
    operation.finished_at = now
    operation.updated_at = now
    db.commit()


def _succeed_sync_operation(
    operation: SandboxAdminOperation,
    *,
    step: str = "completed",
) -> None:
    now = db_now_naive()
    operation.status = "succeeded"
    operation.step = step
    operation.error_code = ""
    operation.error_summary = ""
    operation.locked_by = ""
    operation.lease_token = ""
    operation.lease_expires_at = None
    operation.finished_at = now
    operation.updated_at = now


def _runtime_datetime(value: object) -> datetime | None:
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


def _lease_session_summary(
    lease: SandboxLease | None,
    grant: SandboxAccessGrant | None,
) -> str:
    if lease is None:
        return ""
    digest = hashlib.sha256(
        str(lease.chat_stream_id).encode("utf-8")
    ).hexdigest()[:12]
    platform = str(grant.platform if grant is not None else "")[:32]
    chat_type = str(grant.chat_type if grant is not None else "")[:16]
    if not platform or not chat_type:
        return f"session:{digest}"
    return f"{platform}:{chat_type}:{digest}"


def _lease_summary(
    lease: SandboxLease | None,
    fact: Mapping[str, object] | None,
    grant: SandboxAccessGrant | None = None,
) -> dict[str, object]:
    """合并 Server 账本与 sandboxd 事实，只投影管理页安全字段。"""

    runtime = dict(fact or {})
    process_ids = runtime.get("active_process_ids")
    active_process_count = (
        len(process_ids)
        if isinstance(process_ids, list)
        and all(isinstance(item, str) for item in process_ids)
        else 0
    )
    last_active_at = (
        lease.last_active_at
        if lease is not None
        else _runtime_datetime(runtime.get("last_active_at_unix"))
    )
    idle_expires_at = (
        lease.idle_expires_at
        if lease is not None
        else _runtime_datetime(runtime.get("idle_expires_at_unix"))
    )
    max_expires_at = (
        lease.max_expires_at
        if lease is not None
        else _runtime_datetime(runtime.get("max_expires_at_unix"))
    )
    try:
        quota_generation = int(runtime.get("quota_generation") or 0)
    except (TypeError, ValueError):
        quota_generation = 0
    return {
        "lease_id": str(
            lease.lease_id if lease is not None else runtime.get("lease_id") or ""
        )[:64],
        "session_summary": _lease_session_summary(lease, grant),
        "workspace_id": str(
            lease.workspace_id
            if lease is not None
            else runtime.get("workspace_id") or ""
        )[:36],
        "profile_id": str(
            lease.profile_id
            if lease is not None
            else runtime.get("profile_id") or ""
        )[:32],
        "status": str(
            lease.status
            if lease is not None
            else runtime.get("status") or "missing"
        )[:16],
        "image_digest": str(
            lease.image_digest
            if lease is not None and lease.image_digest
            else runtime.get("image_digest") or ""
        )[:255],
        "catalog_generation": str(
            lease.catalog_generation
            if lease is not None
            else runtime.get("catalog_generation") or ""
        )[:64],
        "policy_sha256": str(
            lease.policy_sha256
            if lease is not None
            else runtime.get("policy_sha256") or ""
        )[:64],
        "controller_epoch": str(
            lease.controller_epoch
            if lease is not None and lease.controller_epoch
            else runtime.get("controller_epoch") or ""
        )[:64],
        "quota_generation": max(0, quota_generation),
        "runtime_present": runtime.get("present") is True,
        "runtime_running": runtime.get("running") is True,
        "active_process_count": active_process_count,
        "last_active_at": _iso(last_active_at),
        "idle_expires_at": _iso(idle_expires_at),
        "max_expires_at": _iso(max_expires_at),
        "last_error_code": (
            str(lease.last_error_code or "")[:64]
            if lease is not None
            else ""
        ),
        "last_error_summary": (
            str(lease.last_error_summary or "")[:255]
            if lease is not None
            else ""
        ),
    }


def _settle_active_runs(
    db: Session,
    *,
    lease_id: str,
    reason: str,
) -> list[str]:
    now = db_now_naive()
    rows = (
        db.query(SandboxRun)
        .filter(
            SandboxRun.lease_id == lease_id,
            SandboxRun.status.in_(("pending", "running")),
        )
        .all()
    )
    for row in rows:
        row.status = "cancelled"
        row.process_state = "lost"
        row.termination_reason = str(reason)[:64]
        row.finished_at = now
        row.last_seen_at = now
        row.updated_at = now
    return [str(row.run_id) for row in rows]


@router.get("/status")
def sandbox_status(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return {
        "feature": {
            "infrastructure_enable_allowed": settings.get_bool(
                "sandbox.infrastructure_enable_allowed",
                False,
            ),
            "session_execution_allowed": settings.get_bool(
                "sandbox.session_execution_allowed",
                False,
            ),
            "developer_network_allowed": settings.get_bool(
                "sandbox.developer_network_allowed",
                False,
            ),
            "enabled": bool(resolve_sandbox_setting(db, "sandbox.enabled")),
            "exec_enabled": bool(resolve_sandbox_setting(
                db,
                "sandbox.exec_enabled",
            )),
            "group_enabled": bool(resolve_sandbox_setting(
                db,
                "sandbox.group_enabled",
            )),
            "group_enabled_editable": True,
        },
        "controller": _controller_status(db),
        "usage": _usage_summary(db),
        "limits": {
            "workspace_default_quota_bytes": int(resolve_sandbox_setting(
                db,
                "sandbox.workspace_quota_bytes",
            )),
            "asset_max_bytes": int(resolve_sandbox_setting(
                db,
                "sandbox.asset_max_bytes",
            )),
            "total_quota_bytes": int(resolve_sandbox_setting(
                db,
                "sandbox.total_quota_bytes",
            )),
        },
        "disk_watermark": {
            "max_used_percent": int(resolve_sandbox_setting(
                db,
                "sandbox.disk_max_percent",
            )),
            "min_free_bytes": int(resolve_sandbox_setting(
                db,
                "sandbox.disk_min_free_bytes",
            )),
        },
        "current_runs": _recent_runs(
            db,
            statuses=("pending", "running"),
            limit=20,
        ),
        "recent_failures": _recent_runs(db, statuses=("failed",), limit=20),
    }


@router.put("/features")
def update_sandbox_features(
    body: SandboxFeatureRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    infrastructure_allowed = settings.get_bool(
        "sandbox.infrastructure_enable_allowed",
        False,
    )
    previous = {
        "enabled": bool(resolve_sandbox_setting(db, "sandbox.enabled")),
        "exec_enabled": bool(resolve_sandbox_setting(db, "sandbox.exec_enabled")),
        "group_enabled": bool(resolve_sandbox_setting(
            db,
            "sandbox.group_enabled",
        )),
    }
    requested_group_enabled = (
        previous["group_enabled"]
        if body.group_enabled is None
        else bool(body.group_enabled)
    )
    if body.exec_enabled and not body.enabled:
        raise HTTPException(400, "启用 Exec 时必须同时启用 Sandbox 总开关")
    if body.group_enabled is True and not body.enabled:
        raise HTTPException(400, "启用群聊时必须同时启用 Sandbox 总开关")
    target_group_enabled = bool(requested_group_enabled and body.enabled)
    if (
        body.enabled
        or body.exec_enabled
        or target_group_enabled
    ) and not infrastructure_allowed:
        raise HTTPException(
            409,
            "宿主基础设施硬上限未允许，Web 不能启用 Sandbox",
        )
    try:
        values = {
            "sandbox.enabled": bool(body.enabled),
            "sandbox.exec_enabled": bool(body.exec_enabled and body.enabled),
            "sandbox.group_enabled": target_group_enabled,
        }
        for key, value in values.items():
            row = db.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(
                    key=key,
                    value="true" if value else "false",
                    description="Sandbox 专用管理页业务开关",
                )
                db.add(row)
            else:
                row.value = "true" if value else "false"
        db.commit()
        settings.invalidate()
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "Sandbox 开关更新失败") from exc
    audit_request(
        db,
        request,
        "sandbox_feature_update",
        "sandbox",
        "global",
        {"previous": previous, "current": values, "reason": body.reason},
    )
    return {
        "ok": True,
        "feature": {
            "infrastructure_enable_allowed": infrastructure_allowed,
            "session_execution_allowed": settings.get_bool(
                "sandbox.session_execution_allowed",
                False,
            ),
            "developer_network_allowed": settings.get_bool(
                "sandbox.developer_network_allowed",
                False,
            ),
            "enabled": values["sandbox.enabled"],
            "exec_enabled": values["sandbox.exec_enabled"],
            "group_enabled": values["sandbox.group_enabled"],
        },
    }


@router.get("/sessions")
def list_sandbox_sessions(
    search: str = Query(default="", max_length=255),
    chat_type: Literal["private", "group", "all"] = Query(default="private"),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """列出真实出现过的会话，不把消息发送者当作授权主体。"""

    search_text = str(search or "").strip().lower()
    candidates: dict[str, dict[str, object]] = {}

    def add(
        *,
        session_id: object,
        user_id: object,
        sender_name: object,
        session_name: object,
        created_at,
        meta_json: object,
    ) -> None:
        raw_session = str(session_id or "").strip()
        if not raw_session:
            return
        try:
            meta = json.loads(str(meta_json or "{}"))
        except (TypeError, json.JSONDecodeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        client_meta = meta.get("client_meta")
        if not isinstance(client_meta, dict):
            client_meta = {}
        meta_chat_type = str(
            meta.get("chat_type") or client_meta.get("chat_type") or ""
        ).strip().lower()
        if meta_chat_type and meta_chat_type not in {"private", "group"}:
            return
        inferred_chat_type = (
            "group"
            if (
                meta_chat_type == "group"
                or raw_session.startswith("group_")
                or raw_session.endswith(":group")
            )
            else "private"
        )
        if chat_type != "all" and inferred_chat_type != chat_type:
            return
        platform = str(
            meta.get("platform") or client_meta.get("platform") or "qq"
        ).strip().lower()
        try:
            identity = canonical_sandbox_identity(
                platform=platform,
                chat_type=inferred_chat_type,
                session_id=raw_session,
            )
        except Exception:
            return
        sender_meta = meta.get("sender")
        if not isinstance(sender_meta, dict):
            sender_meta = {}
        actor_source = (
            sender_meta.get("id")
            if identity.chat_type == "group"
            else user_id
        )
        actor_user_id = str(actor_source or "").strip()
        sender_label = str(sender_name or "").strip()
        group_label = str(session_name or "").strip()
        label = (
            group_label
            if identity.chat_type == "group"
            else sender_label
        )
        haystack = (
            f"{identity.chat_stream_id} {identity.external_session_id} "
            f"{actor_user_id} {sender_label} {group_label}"
        ).lower()
        if search_text and search_text not in haystack:
            return
        old = candidates.get(identity.chat_stream_id)
        if old is None:
            candidates[identity.chat_stream_id] = {
                "chat_stream_id": identity.chat_stream_id,
                "platform": identity.platform,
                "chat_type": identity.chat_type,
                "session_id": identity.external_session_id,
                "actor_user_id": actor_user_id,
                "label": label or identity.external_session_id,
                "recent_at": _iso(created_at),
                "_created_at": created_at,
            }
            return
        if label and (
            identity.chat_type == "group"
            or str(old.get("label") or "") == identity.external_session_id
        ):
            old["label"] = label
        if actor_user_id:
            old["actor_user_id"] = actor_user_id
        if old.get("_created_at") is None or (
            created_at is not None and created_at > old["_created_at"]
        ):
            old["recent_at"] = _iso(created_at)
            old["_created_at"] = created_at

    for row in db.query(ChatLog).order_by(ChatLog.id.desc()).limit(5000).all():
        add(
            session_id=row.session_id,
            user_id=row.user_id,
            sender_name=row.sender_name,
            session_name=row.session_name,
            created_at=row.created_at,
            meta_json=row.meta_json,
        )
    for row in (
        db.query(ConversationTurn)
        .order_by(ConversationTurn.id.desc())
        .limit(5000)
        .all()
    ):
        add(
            session_id=row.session_id,
            user_id=row.user_id,
            sender_name="",
            session_name="",
            created_at=row.created_at,
            meta_json=row.meta_json,
        )
    for grant in db.query(SandboxAccessGrant).all():
        if chat_type != "all" and grant.chat_type != chat_type:
            continue
        if grant.chat_stream_id not in candidates:
            candidates[grant.chat_stream_id] = {
                "chat_stream_id": grant.chat_stream_id,
                "platform": grant.platform,
                "chat_type": grant.chat_type,
                "session_id": grant.external_session_id,
                "actor_user_id": "",
                "label": grant.external_session_id,
                "recent_at": _iso(grant.updated_at),
                "_created_at": grant.updated_at,
            }
    items = sorted(
        candidates.values(),
        key=lambda item: (str(item.get("recent_at") or ""), str(item["chat_stream_id"])),
        reverse=True,
    )[:limit]
    for item in items:
        item.pop("_created_at", None)
    return {"items": items}


@router.get("/access-grants")
def list_sandbox_access_grants(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    rows = db.query(SandboxAccessGrant).order_by(
        SandboxAccessGrant.updated_at.desc(),
    ).all()
    items = []
    for grant in rows:
        workspace = db.get(Workspace, str(grant.workspace_id or ""))
        binding = db.get(WorkspaceQuotaBinding, str(grant.workspace_id or ""))
        runtime_binding = db.get(
            WorkspaceRuntimeQuotaBinding,
            str(grant.workspace_id or ""),
        )
        items.append(_grant_summary(
            grant,
            workspace,
            binding,
            runtime_binding,
        ))
    return {"items": items}


@router.post(
    "/access-grants",
    status_code=status.HTTP_202_ACCEPTED,
)
def set_sandbox_access_grant(
    body: SandboxAccessRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    try:
        result = SandboxAdminService(db).enqueue_access_change(
            request_id=body.request_id,
            platform=body.platform,
            chat_type=body.chat_type,
            session_id=body.session_id,
            capability=body.capability,
            execution_profile=body.execution_profile,
            quota_bytes=body.quota_bytes,
            expected_version=body.expected_version,
            reason=body.reason,
            actor=admin_user,
        )
        db.commit()
    except SandboxAdminRequestError as exc:
        db.rollback()
        raise _admin_error(exc) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "Sandbox 授权操作入队失败") from exc
    audit_request(
        db,
        request,
        "sandbox_access_enqueue",
        "sandbox_session",
        result.operation.chat_stream_id,
        {
            "operation_id": result.operation.operation_id,
            "capability": body.capability,
            "execution_profile": body.execution_profile,
            "quota_bytes": body.quota_bytes,
            "created": result.created,
            "reason": body.reason,
        },
    )
    return {
        "accepted": True,
        "created": result.created,
        "operation": _operation_summary(result.operation),
    }


@router.get("/workspaces")
def list_sandbox_workspaces(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    grants_by_workspace: dict[str, list[str]] = {}
    for workspace_id, chat_stream_id in db.query(
        SandboxAccessGrant.workspace_id,
        SandboxAccessGrant.chat_stream_id,
    ).filter(SandboxAccessGrant.workspace_id.is_not(None)).all():
        grants_by_workspace.setdefault(str(workspace_id), []).append(str(chat_stream_id))
    items = []
    for workspace in db.query(Workspace).order_by(Workspace.created_at.desc()).all():
        binding = db.get(WorkspaceQuotaBinding, workspace.id)
        runtime_binding = db.get(
            WorkspaceRuntimeQuotaBinding,
            workspace.id,
        )
        items.append({
            "workspace_id": workspace.id,
            "status": workspace.status,
            "used_bytes": int(workspace.used_bytes or 0),
            "quota_bytes": int(workspace.quota_bytes or 0),
            "sessions": sorted(grants_by_workspace.get(workspace.id, [])),
            "project_id": int(binding.project_id) if binding else None,
            "desired_quota_bytes": int(binding.desired_quota_bytes) if binding else None,
            "applied_quota_bytes": int(binding.applied_quota_bytes or 0) if binding else None,
            "quota_status": binding.status if binding else "missing",
            "quota_generation": int(binding.generation) if binding else None,
            "runtime_project_id": (
                int(runtime_binding.project_id)
                if runtime_binding
                else None
            ),
            "runtime_desired_quota_bytes": (
                int(runtime_binding.desired_quota_bytes)
                if runtime_binding
                else None
            ),
            "runtime_applied_quota_bytes": (
                int(runtime_binding.applied_quota_bytes or 0)
                if runtime_binding
                else None
            ),
            "runtime_quota_status": (
                runtime_binding.status
                if runtime_binding
                else "missing"
            ),
            "last_error_code": binding.last_error_code if binding else "",
            "created_at": _iso(workspace.created_at),
            "updated_at": _iso(workspace.updated_at),
        })
    return {"items": items}


@router.post(
    "/workspaces/{workspace_id}/quota",
    status_code=status.HTTP_202_ACCEPTED,
)
def set_workspace_quota(
    body: SandboxQuotaRequest,
    request: Request,
    workspace_id: str = Path(min_length=36, max_length=36),
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    try:
        result = SandboxAdminService(db).enqueue_quota_change(
            request_id=body.request_id,
            workspace_id=workspace_id,
            quota_bytes=body.quota_bytes,
            reason=body.reason,
            actor=admin_user,
        )
        db.commit()
    except SandboxAdminRequestError as exc:
        db.rollback()
        raise _admin_error(exc) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "Workspace 配额操作入队失败") from exc
    audit_request(
        db,
        request,
        "sandbox_quota_enqueue",
        "workspace",
        workspace_id,
        {
            "operation_id": result.operation.operation_id,
            "quota_bytes": body.quota_bytes,
            "created": result.created,
            "reason": body.reason,
        },
    )
    return {
        "accepted": True,
        "created": result.created,
        "operation": _operation_summary(result.operation),
    }


@router.get("/operations")
def list_sandbox_operations(
    operation_status: Literal[
        "pending", "running", "succeeded", "failed", "cancelled"
    ] | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    query = db.query(SandboxAdminOperation)
    if operation_status:
        query = query.filter(SandboxAdminOperation.status == operation_status)
    rows = query.order_by(SandboxAdminOperation.created_at.desc()).limit(limit).all()
    return {"items": [_operation_summary(row) for row in rows]}


@router.get("/operations/{operation_id}")
def get_sandbox_operation(
    operation_id: str = Path(
        min_length=8,
        max_length=64,
        pattern=r"^sbxop_[A-Za-z0-9_-]+$",
    ),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    row = db.get(SandboxAdminOperation, operation_id)
    if row is None:
        raise HTTPException(404, "Sandbox 管理操作不存在")
    return {"operation": _operation_summary(row)}


@router.get("/audit-logs")
def list_sandbox_audit_logs(
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    rows = (
        db.query(AdminAuditLog)
        .filter(AdminAuditLog.action.like("sandbox_%"))
        .order_by(AdminAuditLog.id.desc())
        .limit(limit)
        .all()
    )
    items = []
    for row in rows:
        try:
            detail = json.loads(str(row.detail_json or "{}"))
        except (TypeError, json.JSONDecodeError):
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        items.append({
            "id": row.id,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "detail": detail,
            "admin_user": row.admin_user,
            "ip_address": row.ip_address,
            "created_at": _iso(row.created_at),
        })
    return {"items": items}


@router.get("/leases")
def list_sandbox_leases(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    backend = _sandbox_admin_backend(db)
    try:
        response = backend.list_leases()
    except SandboxServiceError as exc:
        raise HTTPException(503, "Sandbox Lease 列表暂时不可用") from exc
    finally:
        backend.close()
    data = response.get("data")
    raw_facts = data.get("leases") if isinstance(data, Mapping) else None
    if not isinstance(raw_facts, list):
        raise HTTPException(503, "Sandbox Lease 控制面返回了无效列表")

    facts: dict[str, Mapping[str, object]] = {}
    for raw in raw_facts:
        if not isinstance(raw, Mapping):
            raise HTTPException(503, "Sandbox Lease 控制面返回了无效事实")
        lease_id = str(raw.get("lease_id") or "")
        if (
            not lease_id.startswith("sbxlease_")
            or not 10 <= len(lease_id) <= 64
            or lease_id in facts
        ):
            raise HTTPException(503, "Sandbox Lease 控制面返回了无效标识")
        facts[lease_id] = raw

    leases = {
        str(row.lease_id): row
        for row in db.query(SandboxLease).all()
    }
    items = []
    for lease_id in sorted(set(leases) | set(facts)):
        lease = leases.get(lease_id)
        grant = (
            db.get(SandboxAccessGrant, str(lease.grant_id))
            if lease is not None
            else None
        )
        items.append(_lease_summary(lease, facts.get(lease_id), grant))
    return {"items": items}


def _perform_lease_action(
    *,
    action: Literal["stop", "destroy", "recreate"],
    body: SandboxLeaseActionRequest,
    request: Request,
    lease_id: str,
    db: Session,
    admin_user: str,
) -> dict[str, object]:
    lease = db.get(SandboxLease, lease_id)
    if lease is None:
        raise HTTPException(404, "Sandbox Lease 不存在")
    operation_type = f"lease_{action}"
    try:
        operation, replayed = _prepare_sync_operation(
            db,
            request_id=body.request_id,
            operation_type=operation_type,
            chat_stream_id=str(lease.chat_stream_id),
            workspace_id=str(lease.workspace_id),
            reason=body.reason,
            actor=admin_user,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "Sandbox Lease 管理操作建账失败") from exc

    if replayed:
        grant = db.get(SandboxAccessGrant, str(lease.grant_id))
        return {
            "ok": True,
            "replayed": True,
            "lease": _lease_summary(lease, None, grant),
            "environment_action": "",
            "data_preserved": True,
        }

    backend = _sandbox_admin_backend(db)
    try:
        callback = {
            "stop": backend.stop_lease,
            "destroy": backend.destroy_lease,
            "recreate": backend.recreate_lease,
        }[action]
        response = callback(lease_id, request_id=body.request_id)
        raw_data = response.get("data")
        if not isinstance(raw_data, Mapping):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 管理响应结构无效",
                retryable=True,
                stop=False,
            )
        data = dict(raw_data)
        if str(data.get("lease_id") or "") != lease_id:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 管理响应归属无效",
                retryable=True,
                stop=False,
            )

        reason = f"admin_lease_{action}"
        now = db_now_naive()
        if action in {"stop", "destroy"}:
            # lease_recycled=False 表示控制面上已无容器/网络/快照——Lease
            # 先前已被回收（TTL 到期、controller 重启等）。sandboxd 对“存在
            # 但无法删除”的容器会直接抛错，因此 False 是安全的幂等成功，
            # 管理操作照常收敛 DB 状态，不得 503。
            if (
                not isinstance(data.get("lease_recycled"), bool)
                or data.get("workspace_preserved") is not True
                or data.get("runtime_preserved") is not True
                or str(data.get("termination_reason") or "") != reason
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox Lease 未确认完成安全回收",
                    retryable=True,
                    stop=False,
                )
            lease.status = "stopped" if action == "stop" else "destroyed"
            lease.stopped_at = now
            lease.reconciled_at = now
            lease.last_error_code = reason
            lease.last_error_summary = (
                "Sandbox Lease 已由管理员停止"
                if action == "stop"
                else "Sandbox Lease 已由管理员销毁"
            )
            environment_action = ""
        else:
            environment = data.get("environment")
            if (
                str(data.get("workspace_id") or "") != str(lease.workspace_id)
                or str(data.get("profile_id") or "") != str(lease.profile_id)
                or str(data.get("catalog_generation") or "")
                != str(lease.catalog_generation)
                or str(data.get("policy_sha256") or "")
                != str(lease.policy_sha256)
                or data.get("present") is not True
                or data.get("running") is not True
                or str(data.get("status") or "") not in {"active", "idle"}
                or not isinstance(environment, Mapping)
                or environment.get("ready") is not True
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "重建后的 Sandbox Lease 事实无效",
                    retryable=True,
                    stop=False,
                )
            lease.status = str(data["status"])
            lease.image_digest = str(data.get("image_digest") or "")[:255]
            lease.controller_epoch = str(
                data.get("controller_epoch") or ""
            )[:64]
            lease.last_active_at = (
                _runtime_datetime(data.get("last_active_at_unix"))
                or now
            )
            lease.idle_expires_at = _runtime_datetime(
                data.get("idle_expires_at_unix")
            )
            lease.max_expires_at = _runtime_datetime(
                data.get("max_expires_at_unix")
            )
            lease.stopped_at = None
            lease.reconciled_at = now
            lease.last_error_code = ""
            lease.last_error_summary = ""
            environment_action = str(environment.get("action") or "")[:32]

        _settle_active_runs(db, lease_id=lease_id, reason=reason)
        current_operation = db.get(
            SandboxAdminOperation,
            operation.operation_id,
        )
        if current_operation is None or current_operation.status != "running":
            raise RuntimeError("Sandbox Lease 管理操作账本已失效")
        _succeed_sync_operation(current_operation)
        db.commit()
    except SandboxServiceError as exc:
        _fail_sync_operation(
            db,
            operation.operation_id,
            code=exc.code.value,
            summary=exc.summary,
        )
        if exc.code is SandboxErrorCode.AUTHORIZATION_FAILED:
            raise HTTPException(404, "Sandbox Lease 不存在") from exc
        raise HTTPException(503, "Sandbox Lease 管理操作未能完成") from exc
    except Exception as exc:
        _fail_sync_operation(
            db,
            operation.operation_id,
            code="ledger_settlement_failed",
            summary="Sandbox Lease 管理账本结算失败",
        )
        raise HTTPException(503, "Sandbox Lease 管理账本结算失败") from exc
    finally:
        backend.close()

    db.expire_all()
    lease = db.get(SandboxLease, lease_id)
    grant = (
        db.get(SandboxAccessGrant, str(lease.grant_id))
        if lease is not None
        else None
    )
    audit_request(
        db,
        request,
        f"sandbox_lease_{action}",
        "sandbox_lease",
        lease_id,
        {
            "request_id": body.request_id,
            "operation_id": operation.operation_id,
            "reason": body.reason,
            "workspace_preserved": True,
            "runtime_preserved": True,
        },
    )
    return {
        "ok": True,
        "replayed": False,
        "lease": _lease_summary(lease, data, grant),
        "environment_action": environment_action,
        "data_preserved": True,
    }


@router.post("/leases/{lease_id}/stop")
def stop_sandbox_lease(
    body: SandboxLeaseActionRequest,
    request: Request,
    lease_id: str = Path(
        min_length=10,
        max_length=64,
        pattern=r"^sbxlease_[A-Za-z0-9_-]+$",
    ),
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    return _perform_lease_action(
        action="stop",
        body=body,
        request=request,
        lease_id=lease_id,
        db=db,
        admin_user=admin_user,
    )


@router.post("/leases/{lease_id}/destroy")
def destroy_sandbox_lease(
    body: SandboxLeaseActionRequest,
    request: Request,
    lease_id: str = Path(
        min_length=10,
        max_length=64,
        pattern=r"^sbxlease_[A-Za-z0-9_-]+$",
    ),
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    return _perform_lease_action(
        action="destroy",
        body=body,
        request=request,
        lease_id=lease_id,
        db=db,
        admin_user=admin_user,
    )


@router.post("/leases/{lease_id}/recreate")
def recreate_sandbox_lease(
    body: SandboxLeaseActionRequest,
    request: Request,
    lease_id: str = Path(
        min_length=10,
        max_length=64,
        pattern=r"^sbxlease_[A-Za-z0-9_-]+$",
    ),
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    return _perform_lease_action(
        action="recreate",
        body=body,
        request=request,
        lease_id=lease_id,
        db=db,
        admin_user=admin_user,
    )


@router.get("/runs")
def list_sandbox_runs(
    status: _RUN_STATUS | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    statuses = (status,) if status else None
    return {"items": _recent_runs(db, statuses=statuses, limit=limit)}


@router.post("/runs/{run_id}/cancel")
def cancel_sandbox_run(
    request: Request,
    run_id: str = Path(
        min_length=8,
        max_length=64,
        pattern=r"^sbxrun_[A-Za-z0-9_-]+$",
    ),
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    row = db.get(SandboxRun, run_id)
    if row is None:
        raise HTTPException(404, "Sandbox 运行不存在")
    if str(row.execution_mode or "") == "lease":
        # lease 进程不存在单 Run 取消端点（sandboxd /runs/cancel 只查
        # oneshot RunStore）；v1 的可靠终止边界是整个 Lease，与
        # sandbox_terminate 语义一致地走管理端 Lease 停止。
        if str(row.status) in {"completed", "failed", "cancelled"}:
            return {
                "ok": True,
                "data": {"run_id": run_id, "status": str(row.status)},
            }
        lease_id = str(row.lease_id or "")
        if not lease_id:
            raise HTTPException(503, "Sandbox 运行缺少 Lease 归属")
        _perform_lease_action(
            action="stop",
            body=SandboxLeaseActionRequest(
                request_id=f"sbxcancel_{secrets.token_hex(12)}",
                reason=f"run_cancel:{run_id}"[:255],
            ),
            request=request,
            lease_id=lease_id,
            db=db,
            admin_user=admin_user,
        )
        db.expire_all()
        current = db.get(SandboxRun, run_id)
        status_value = str(current.status) if current is not None else ""
        audit_request(
            db,
            request,
            "sandbox_cancel_run",
            "sandbox_run",
            run_id,
            {"status": status_value, "termination_scope": "lease"},
        )
        return {
            "ok": True,
            "data": {
                "run_id": run_id,
                "status": status_value,
                "termination_scope": "lease",
                "lease_id": lease_id,
            },
        }
    backend = _sandbox_backend(db)
    try:
        response = backend.cancel_run(
            run_id,
            request_id=f"admin-cancel-{run_id}"[:64],
        )
    except SandboxServiceError as exc:
        if exc.code is SandboxErrorCode.AUTHORIZATION_FAILED:
            raise HTTPException(404, "Sandbox 运行不存在") from exc
        raise HTTPException(503, "Sandbox 取消暂时不可用") from exc
    finally:
        backend.close()
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    safe_data = {
        key: data.get(key)
        for key in (
            "run_id",
            "workspace_id",
            "image_digest",
            "status",
            "created_at_unix",
            "started_at_unix",
            "finished_at_unix",
            "error_code",
        )
        if key in data
    }
    audit_request(
        db,
        request,
        "sandbox_cancel_run",
        "sandbox_run",
        run_id,
        {"status": safe_data.get("status")},
    )
    return {"ok": True, "data": safe_data}


@router.post("/kill-switch")
def sandbox_kill_switch(
    body: SandboxKillSwitchRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    previous = {
        "enabled": bool(resolve_sandbox_setting(db, "sandbox.enabled")),
        "exec_enabled": bool(resolve_sandbox_setting(
            db,
            "sandbox.exec_enabled",
        )),
        "group_enabled": bool(resolve_sandbox_setting(
            db,
            "sandbox.group_enabled",
        )),
    }
    try:
        operation, replayed = _prepare_sync_operation(
            db,
            request_id=body.request_id,
            operation_type="kill_switch",
            reason=body.reason,
            actor=admin_user,
        )
        if replayed:
            started_at = operation.started_at or operation.created_at
            terminated_lease_count = (
                db.query(func.count(SandboxLease.lease_id))
                .filter(
                    SandboxLease.last_error_code == "kill_switch",
                    SandboxLease.reconciled_at >= started_at,
                )
                .scalar()
            )
            terminated_run_count = (
                db.query(func.count(SandboxRun.run_id))
                .filter(
                    SandboxRun.termination_reason == "kill_switch",
                    SandboxRun.finished_at >= started_at,
                )
                .scalar()
            )
            lease_count = int(terminated_lease_count or 0)
            run_count = int(terminated_run_count or 0)
            return {
                "ok": True,
                "replayed": True,
                "feature": {
                    "enabled": False,
                    "exec_enabled": False,
                    "group_enabled": False,
                },
                "terminated_count": lease_count + run_count,
                "failed_count": 0,
                "terminated_lease_count": lease_count,
                "terminated_run_count": run_count,
                "failed_lease_count": 0,
                "failed_run_count": 0,
                "data_preserved": True,
            }
        for key in (
            "sandbox.enabled",
            "sandbox.exec_enabled",
            "sandbox.group_enabled",
        ):
            row = db.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(
                    key=key,
                    value="false",
                    description="Sandbox 管理端无损关闭开关",
                )
                db.add(row)
            else:
                row.value = "false"
        db.commit()
        settings.invalidate()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "Sandbox 关闭开关暂时不可用") from exc

    operation_id = str(operation.operation_id)
    active_leases = (
        db.query(SandboxLease)
        .filter(SandboxLease.status.in_(
            ("provisioning", "active", "idle", "stopping")
        ))
        .all()
    )
    expected_lease_ids = {
        str(lease.lease_id)
        for lease in active_leases
    }

    terminated_lease_ids: set[str] = set()
    failed_lease_ids: set[str] = set()
    admin_error_summary = ""
    admin_call_confirmed = False
    admin_backend = _sandbox_admin_backend(db)
    try:
        response = admin_backend.terminate_all_leases(
            request_id=body.request_id,
            reason="kill_switch",
        )
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise ValueError("Sandbox Lease 全量终止响应结构无效")

        def identifier_set(key: str) -> set[str]:
            raw = data.get(key)
            if (
                not isinstance(raw, list)
                or not all(
                    isinstance(item, str)
                    and item.startswith("sbxlease_")
                    and 10 <= len(item) <= 64
                    for item in raw
                )
                or len(raw) != len(set(raw))
            ):
                raise ValueError(
                    f"Sandbox Lease 全量终止响应 {key} 无效"
                )
            return set(raw)

        terminated_lease_ids = identifier_set(
            "terminated_lease_ids"
        )
        failed_lease_ids = identifier_set("failed_lease_ids")
        overlap = terminated_lease_ids & failed_lease_ids
        if overlap:
            terminated_lease_ids -= overlap
            failed_lease_ids |= overlap
            admin_error_summary = "sandboxd 返回了冲突的 Lease 终止事实"
        failed_lease_ids |= (
            expected_lease_ids
            - terminated_lease_ids
            - failed_lease_ids
        )
        admin_call_confirmed = True
    except (SandboxServiceError, TypeError, ValueError) as exc:
        failed_lease_ids |= expected_lease_ids
        admin_error_summary = (
            exc.summary
            if isinstance(exc, SandboxServiceError)
            else "Sandbox Lease 全量终止响应无效"
        )
    finally:
        admin_backend.close()

    active_oneshot_runs = (
        db.query(SandboxRun)
        .filter(
            SandboxRun.status.in_(("pending", "running")),
            SandboxRun.execution_mode == "oneshot",
            SandboxRun.lease_id.is_(None),
        )
        .all()
    )
    terminated_oneshot_run_ids: set[str] = set()
    failed_oneshot_run_ids: set[str] = set()
    regular_backend = _sandbox_backend(db) if active_oneshot_runs else None
    try:
        for run in active_oneshot_runs:
            run_id = str(run.run_id)
            cancel_request_id = "kill_" + hashlib.sha256(
                f"{body.request_id}\0{run_id}".encode("utf-8")
            ).hexdigest()[:32]
            try:
                response = regular_backend.cancel_run(
                    run_id,
                    request_id=cancel_request_id,
                )
                data = response.get("data")
                if (
                    not isinstance(data, Mapping)
                    or str(data.get("run_id") or "") != run_id
                    or str(data.get("status") or "")
                    not in {"cancelling", "cancelled"}
                ):
                    raise ValueError("Sandbox 单次运行取消响应无效")
            except (SandboxServiceError, TypeError, ValueError):
                failed_oneshot_run_ids.add(run_id)
            else:
                terminated_oneshot_run_ids.add(run_id)
    finally:
        if regular_backend is not None:
            regular_backend.close()

    try:
        db.expire_all()
        now = db_now_naive()
        terminated_lease_run_ids: set[str] = set()
        failed_lease_run_ids: set[str] = set()
        for lease_id in terminated_lease_ids:
            lease = db.get(SandboxLease, lease_id)
            if lease is not None and str(lease.status) in {
                "provisioning",
                "active",
                "idle",
                "stopping",
            }:
                lease.status = "stopped"
                lease.stopped_at = now
                lease.reconciled_at = now
                lease.last_error_code = "kill_switch"
                lease.last_error_summary = (
                    "Sandbox kill switch 已确认回收 Lease"
                )
            terminated_lease_run_ids.update(_settle_active_runs(
                db,
                lease_id=lease_id,
                reason="kill_switch",
            ))

        if failed_lease_ids:
            failed_lease_run_ids = {
                str(run_id)
                for (run_id,) in (
                    db.query(SandboxRun.run_id)
                    .filter(
                        SandboxRun.lease_id.in_(failed_lease_ids),
                        SandboxRun.status.in_(("pending", "running")),
                    )
                    .all()
                )
            }

        for run_id in terminated_oneshot_run_ids:
            run = db.get(SandboxRun, run_id)
            if run is None or str(run.status) not in {"pending", "running"}:
                continue
            run.status = "cancelled"
            run.termination_reason = "kill_switch"
            run.finished_at = now
            run.last_seen_at = now
            run.updated_at = now

        terminated_run_ids = (
            terminated_lease_run_ids | terminated_oneshot_run_ids
        )
        failed_run_ids = (
            failed_lease_run_ids | failed_oneshot_run_ids
        )
        failed_lease_count = len(failed_lease_ids)
        if not admin_call_confirmed and failed_lease_count == 0:
            # 即使 Server 账本当前没有 Lease，也不能在 sandboxd 不可达时
            # 宣称宿主侧不存在孤儿 Lease。
            failed_lease_count = 1
        failed_run_count = len(failed_run_ids)
        failed_count = failed_lease_count + failed_run_count
        terminated_lease_count = len(terminated_lease_ids)
        terminated_run_count = len(terminated_run_ids)
        terminated_count = (
            terminated_lease_count + terminated_run_count
        )

        current_operation = db.get(SandboxAdminOperation, operation_id)
        if current_operation is None or current_operation.status != "running":
            raise RuntimeError("Sandbox kill switch 操作账本已失效")
        if failed_count:
            current_operation.status = "failed"
            current_operation.step = "termination_incomplete"
            current_operation.error_code = "termination_incomplete"
            current_operation.error_summary = (
                admin_error_summary
                or "Sandbox kill switch 未能终止全部活动资源"
            )[:255]
            current_operation.locked_by = ""
            current_operation.finished_at = now
            current_operation.updated_at = now
        else:
            _succeed_sync_operation(current_operation)
        db.commit()
    except Exception as exc:
        _fail_sync_operation(
            db,
            operation_id,
            code="ledger_settlement_failed",
            summary="Sandbox kill switch 账本结算失败",
        )
        raise HTTPException(
            503,
            "Sandbox kill switch 账本结算失败",
        ) from exc

    detail = {
        "terminated_count": terminated_count,
        "failed_count": failed_count,
        "terminated_lease_count": terminated_lease_count,
        "terminated_run_count": terminated_run_count,
        "failed_lease_count": failed_lease_count,
        "failed_run_count": failed_run_count,
    }
    audit_request(
        db,
        request,
        "sandbox_kill_switch",
        "sandbox",
        "global",
        {
            "previous": previous,
            "reason": body.reason,
            **detail,
        },
    )
    payload = {
        "ok": True,
        "replayed": False,
        "feature": {
            "enabled": False,
            "exec_enabled": False,
            "group_enabled": False,
        },
        **detail,
        "data_preserved": True,
    }
    if failed_count:
        raise HTTPException(
            503,
            {
                "code": "termination_incomplete",
                "message": (
                    admin_error_summary
                    or "Sandbox kill switch 未能终止全部活动资源"
                ),
                **detail,
                "feature": {
                    "enabled": False,
                    "exec_enabled": False,
                    "group_enabled": False,
                },
                "data_preserved": True,
            },
        )
    return payload

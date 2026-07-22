"""Sandbox 管理状态、无损关闭开关与单次运行取消。"""

from __future__ import annotations

import json
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
    SandboxRun,
    SystemSetting,
    Workspace,
    WorkspaceAsset,
    WorkspaceQuotaBinding,
    get_db,
)
from core.sandbox.client import HttpSandboxdBackend
from core.sandbox.access_policy import canonical_sandbox_identity
from core.sandbox.admin_service import (
    SandboxAdminRequestError,
    SandboxAdminService,
)
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.tool_service import resolve_sandbox_setting
from core.settings_service import settings


router = APIRouter(prefix="/sandbox")
_RUN_STATUS = Literal["pending", "running", "completed", "failed", "cancelled"]


class SandboxKillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=255)


class SandboxFeatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    exec_enabled: bool
    reason: str = Field(default="", max_length=255)


class SandboxAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[!-~]+$")
    platform: str = Field(default="qq", min_length=1, max_length=32)
    chat_type: Literal["private"] = "private"
    session_id: str = Field(min_length=1, max_length=255)
    capability: Literal["off", "workspace", "assets", "exec"]
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
            ready_state: dict[str, object] = {
                "ok": ready.get("status") == "success",
                "docker": raw_data.get("docker") is True,
                "image_id": str(raw_data.get("image_id") or "")[:72],
                "apparmor_profile": str(
                    raw_data.get("apparmor_profile") or ""
                )[:128],
                "disk_used_percent": raw_data.get("disk_used_percent"),
                "disk_free_bytes": raw_data.get("disk_free_bytes"),
            }
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
) -> dict[str, object]:
    return {
        "id": grant.id,
        "chat_stream_id": grant.chat_stream_id,
        "platform": grant.platform,
        "chat_type": grant.chat_type,
        "session_id": grant.external_session_id,
        "workspace_id": grant.workspace_id,
        "capability": grant.capability_level,
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
    }


def _admin_error(error: SandboxAdminRequestError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.summary},
    )


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
            "enabled": bool(resolve_sandbox_setting(db, "sandbox.enabled")),
            "exec_enabled": bool(resolve_sandbox_setting(
                db,
                "sandbox.exec_enabled",
            )),
            "group_enabled": False,
            "group_enabled_editable": False,
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
    if (body.enabled or body.exec_enabled) and not infrastructure_allowed:
        raise HTTPException(
            409,
            "宿主基础设施硬上限未允许，Web 不能启用 Sandbox",
        )
    if body.exec_enabled and not body.enabled:
        raise HTTPException(400, "启用 Exec 时必须同时启用 Sandbox 总开关")
    previous = {
        "enabled": bool(resolve_sandbox_setting(db, "sandbox.enabled")),
        "exec_enabled": bool(resolve_sandbox_setting(db, "sandbox.exec_enabled")),
    }
    try:
        values = {
            "sandbox.enabled": bool(body.enabled),
            "sandbox.exec_enabled": bool(body.exec_enabled and body.enabled),
            "sandbox.group_enabled": False,
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
            "enabled": values["sandbox.enabled"],
            "exec_enabled": values["sandbox.exec_enabled"],
            "group_enabled": False,
        },
    }


@router.get("/sessions")
def list_sandbox_sessions(
    search: str = Query(default="", max_length=255),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """列出真实出现过的私聊 session，不把 user_id 当作授权主体。"""

    search_text = str(search or "").strip().lower()
    candidates: dict[str, dict[str, object]] = {}

    def add(
        *,
        session_id: object,
        user_id: object,
        sender_name: object,
        created_at,
        meta_json: object,
    ) -> None:
        raw_session = str(session_id or "").strip()
        if not raw_session or raw_session.startswith("group_"):
            return
        try:
            meta = json.loads(str(meta_json or "{}"))
        except (TypeError, json.JSONDecodeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta_chat_type = str(meta.get("chat_type") or "").strip().lower()
        if (
            meta_chat_type == "group"
            or raw_session.startswith("group_")
            or raw_session.endswith(":group")
        ):
            return
        platform = str(meta.get("platform") or "qq").strip().lower()
        try:
            identity = canonical_sandbox_identity(
                platform=platform,
                chat_type="private",
                session_id=raw_session,
            )
        except Exception:
            return
        actor_user_id = str(user_id or "").strip()
        name = str(sender_name or "").strip()
        haystack = (
            f"{identity.chat_stream_id} {identity.external_session_id} "
            f"{actor_user_id} {name}"
        ).lower()
        if search_text and search_text not in haystack:
            return
        old = candidates.get(identity.chat_stream_id)
        if old is not None and old.get("_created_at") is not None and (
            created_at is None or old["_created_at"] >= created_at
        ):
            return
        candidates[identity.chat_stream_id] = {
            "chat_stream_id": identity.chat_stream_id,
            "platform": identity.platform,
            "chat_type": identity.chat_type,
            "session_id": identity.external_session_id,
            "actor_user_id": actor_user_id,
            "label": name or identity.external_session_id,
            "recent_at": _iso(created_at),
            "_created_at": created_at,
        }

    for row in db.query(ChatLog).order_by(ChatLog.id.desc()).limit(5000).all():
        add(
            session_id=row.session_id,
            user_id=row.user_id,
            sender_name=row.sender_name,
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
            created_at=row.created_at,
            meta_json=row.meta_json,
        )
    for grant in db.query(SandboxAccessGrant).all():
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
        items.append(_grant_summary(grant, workspace, binding))
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
    _auth=Depends(verify_admin),
):
    row = db.get(SandboxRun, run_id)
    if row is None:
        raise HTTPException(404, "Sandbox 运行不存在")
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
    _auth=Depends(verify_admin),
):
    previous = {
        "enabled": bool(resolve_sandbox_setting(db, "sandbox.enabled")),
        "exec_enabled": bool(resolve_sandbox_setting(
            db,
            "sandbox.exec_enabled",
        )),
    }
    try:
        for key in ("sandbox.enabled", "sandbox.exec_enabled"):
            row = db.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(
                    key=key,
                    value="0",
                    description="Sandbox 管理端无损关闭开关",
                )
                db.add(row)
            else:
                row.value = "0"
        db.commit()
        settings.invalidate()
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "Sandbox 关闭开关暂时不可用") from exc

    active_count = db.query(func.count(SandboxRun.run_id)).filter(
        SandboxRun.status.in_(("pending", "running")),
    ).scalar()
    audit_request(
        db,
        request,
        "sandbox_kill_switch",
        "sandbox",
        "global",
        {
            "previous": previous,
            "reason": body.reason,
            "active_run_count": int(active_count or 0),
        },
    )
    return {
        "ok": True,
        "feature": {"enabled": False, "exec_enabled": False},
        "active_run_count": int(active_count or 0),
        "data_preserved": True,
    }
